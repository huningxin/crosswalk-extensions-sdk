#!/usr/bin/env python

''' This script is used to lint changeset before code checkin
    It get the changeset from the repo and base specified by
    command line arguments. And run cpplint over the changeset.
'''
# TODO(wang16): Only show error for the lines do changed in the changeset

import os
import os.path
import re
import sys

from utils import GitExe, GetCommandOutput, TryAddDepotToolsToPythonPath

PYTHON_EXTS = ['.py']
JS_EXTS = ['.js']

def find_depot_tools_in_path():
  paths = os.getenv('PATH').split(os.path.pathsep)
  for path in paths:
    if os.path.basename(path) == 'depot_tools':
      return path
  return None

def repo_is_dirty():
  return GetCommandOutput([GitExe(), 'diff', 'HEAD']).strip() != ''

def get_tracking_remote():
  branch = [GitExe(), 'branch', '-vv', '-a']
  # The output of git branch -vv will be in format
  # * <branch>     <hash> [<remote>: ahead <n>, behind <m>] <subject>
  #   <branch>     <hash> <subject>
  output = GetCommandOutput(branch)
  branches = output.split('\n')
  for branch in branches:
    # we only need active branch first.
    if not branch.startswith('*'):
      continue
    detail = \
        branch[1:].strip().split(' ', 1)[1].strip().split(' ', 1)[1].strip()
    if detail.startswith('['):
      remote = detail[1:].split(']', 1)[0]
      remote = remote.split(':', 1)[0].strip()
      # verify that remotes/branch or branch is a real branch
      # There is still chance that developer named his commit
      # as [origin/branch], in this case
      exists = [\
          r_branch for r_branch in branches \
              if r_branch.strip().startswith('remotes/'+remote) or \
                 r_branch.strip().startswith(remote)]
      if len(exists) == 0:
        remote = ''
    else:
      remote = ''
    break
  if remote == '':
    if repo_is_dirty():
      remote = 'HEAD'
    else:
      remote = 'HEAD~'
  print 'Base is not specified, '\
        'will use %s as comparasion base for linting' % remote
  return remote

# return pyfiles, others
def get_change_file_list(base):
  diff = [GitExe(), 'diff', '--name-only', base]
  output = GetCommandOutput(diff)
  changes = [line.strip() for line in output.strip().split('\n')]
  pyfiles = []
  jsfiles = []
  others = []
  common_regex = re.compile('common')
  # pylint: disable=W0612
  for change in changes:
    root, ext = os.path.splitext(change)
    if common_regex.match(os.path.dirname(change)):
      print 'Skip common dir'
      continue
    if ext.lower() in PYTHON_EXTS:
      pyfiles.append(change)
    elif ext.lower() in JS_EXTS:
      jsfiles.append(change)
    else:
      others.append(change)
  return pyfiles, jsfiles, others

def do_cpp_lint(changeset, args):
  # Try to import cpplint from depot_tools first
  try:
    import cpplint
  except ImportError:
    TryAddDepotToolsToPythonPath()

  try:
    import cpplint
    import cpplint_chromium
    import gcl
  except ImportError:
    sys.stderr.write("Can't find cpplint, please add your depot_tools "\
                     "to PATH or PYTHONPATH\n")
    raise

  origin_error = cpplint.Error
  def MyError(filename, linenum, category, confidence, message):
    # Skip no header guard  error for MSVC generated files.
    if (filename.endswith('resource.h')):
      sys.stdout.write('Ignored Error:\n  %s(%s):  %s  [%s] [%d]\n' % (
          filename, linenum, message, category, confidence))
    # Skip no header guard  error for ipc messages definition,
    # because they will be included multiple times for different macros.
    elif (filename.endswith('messages.h') and linenum == 0 and
        category == 'build/header_guard'):
      sys.stdout.write('Ignored Error:\n  %s(%s):  %s  [%s] [%d]\n' % (
          filename, linenum, message, category, confidence))
    else:
      origin_error(filename, linenum, category, confidence, message)
  cpplint.Error = MyError

  origin_FileInfo = cpplint.FileInfo
  class MyFileInfo(origin_FileInfo):
    def RepositoryName(self):
      ''' Origin FileInfo find the first .git and take it as project root,
          it's not the case for xwalk, header in xwalk should have guard
          relevant to root dir of chromium project, which is one level
          upper of the origin output of RepositoryName.
      '''
      return origin_FileInfo.RepositoryName(self)

  cpplint.FileInfo = MyFileInfo

  print '_____ do cpp lint'
  if len(changeset) == 0:
    print 'changeset is empty except python files'
    return 0
  # Following code is referencing depot_tools/gcl.py: CMDlint
  # Process cpplints arguments if any.
  filenames = cpplint.ParseArguments(args + changeset)

  white_list = gcl.GetCodeReviewSetting("LINT_REGEX")
  if not white_list:
    white_list = gcl.DEFAULT_LINT_REGEX
  white_regex = re.compile(white_list)
  black_list = gcl.GetCodeReviewSetting("LINT_IGNORE_REGEX")
  if not black_list:
    black_list = gcl.DEFAULT_LINT_IGNORE_REGEX
  black_regex = re.compile(black_list)
  extra_check_functions = [cpplint_chromium.CheckPointerDeclarationWhitespace]
  # pylint: disable=W0212
  cpplint_state = cpplint._cpplint_state
  for filename in filenames:
    if white_regex.match(filename):
      if black_regex.match(filename):
        print "Ignoring file %s" % filename
      else:
        print filename
        cpplint.ProcessFile(filename, cpplint_state.verbose_level,
                            extra_check_functions)
    else:
      print "Skipping file %s" % filename
  print "cpplint errors %d\n" % cpplint_state.error_count
  return cpplint_state.error_count

def do_py_lint(changeset):
  print '_____ do python lint'
  if sys.platform.startswith('win'):
    pylint_cmd = ['pylint.bat']
  else:
    pylint_cmd = ['pylint']
  _has_import_error = False
  error_count = 0;
  for pyfile in changeset:
    if os.path.exists(pyfile) != True:
      print "Skipping file %s: File doesn't exist." % pyfile
      continue
    py_dir, py_name = os.path.split(os.path.abspath(pyfile))
    previous_cwd = os.getcwd()
    os.chdir(py_dir)
    print 'pylint %s' % pyfile
    try:
      output = GetCommandOutput(pylint_cmd + [py_name]).strip()
      if len(output) > 0:
        print output
      else:
        error_count += 1;
    except Exception, e:
      if not _has_import_error and \
          'F0401:' in [error[:6] for error in str(e).splitlines()]:
        _has_import_error = True
      print e
      error_count += 1;
    os.chdir(previous_cwd)
  if _has_import_error:
    print 'You have error for python importing, please check your PYTHONPATH'
  print "pylint errors %d\n" % error_count
  return error_count

def do_js_lint(changeset):
  print '\n_____ do JavaScript lint'
  if sys.platform.startswith('win'):
    jslint_cmd = ['gjslint.exe']
  else:
    jslint_cmd = ['gjslint']
  error_count = 0;
  for jsfile in changeset:
    if os.path.exists(jsfile) != True:
      print "Skipping file %s: File doesn't exist." % jsfile
      continue
    args = ['--strict', '--nojsdoc', '--max_line_length', '100', '--unix_mode']
    js_dir, js_name = os.path.split(os.path.abspath(jsfile))
    previous_cwd = os.getcwd()
    os.chdir(js_dir)
    print 'jslint %s' % jsfile
    args.append(js_name)
    try:
      output = GetCommandOutput(jslint_cmd + args).strip()
      if len(output) > 0:
        print output
      else:
        error_count += 1;
    except Exception, e:
      print e
      error_count += 1;
    os.chdir(previous_cwd)
  print "jslint errors %d\n" % error_count
  return error_count

def do_lint(base, args):
  if base == None:
    base = get_tracking_remote()
  changes_py, changes_js, changes_others = get_change_file_list(base)
  total_erros = 0;
  total_erros += do_cpp_lint(changes_others, args)
  total_erros += do_py_lint(changes_py)
  total_erros += do_js_lint(changes_js)
  print "The total errors found: %d\n" % total_erros
  return total_erros

from optparse import OptionParser, BadOptionError
class PassThroughOptionParser(OptionParser):
  def _process_long_opt(self, rargs, values):
    try:
      OptionParser._process_long_opt(self, rargs, values)
    except BadOptionError, err:
      self.largs.append(err.opt_str)

  def _process_short_opts(self, rargs, values):
    try:
      OptionParser._process_short_opts(self, rargs, values)
    except BadOptionError, err:
      self.largs.append(err.opt_str)

def main():
  option_parser = PassThroughOptionParser()

  option_parser.add_option('--base', default=None,
      help='The base point to get change set. If not specified,' +
           ' it will choose:\r\n' +
           '  1. Active branch\'s tracking branch if exist\n' +
           '  2. HEAD if current repo is dirty\n' +
           '  3. HEAD~ elsewise')

  options, args = option_parser.parse_args()
  
  sys.exit(do_lint(options.base, args))

if __name__ == '__main__':
  main()
