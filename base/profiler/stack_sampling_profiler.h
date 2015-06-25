// Copyright 2015 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef BASE_PROFILER_STACK_SAMPLING_PROFILER_H_
#define BASE_PROFILER_STACK_SAMPLING_PROFILER_H_

#include <string>
#include <vector>

#include "base/base_export.h"
#include "base/callback.h"
#include "base/files/file_path.h"
#include "base/memory/scoped_ptr.h"
#include "base/strings/string16.h"
#include "base/threading/platform_thread.h"
#include "base/time/time.h"

namespace base {

// StackSamplingProfiler periodically stops a thread to sample its stack, for
// the purpose of collecting information about which code paths are
// executing. This information is used in aggregate by UMA to identify hot
// and/or janky code paths.
//
// Sample StackStackSamplingProfiler usage:
//
//   // Create and customize params as desired.
//   base::StackStackSamplingProfiler::SamplingParams params;
//   // Any thread's ID may be passed as the target.
//   base::StackSamplingProfiler profiler(base::PlatformThread::CurrentId()),
//       params);
//
//   // To process the profiles within Chrome rather than via UMA, set a custom
//   // completed callback:
//   base::Callback<void(const std::vector<Profile>&)>
//       thread_safe_callback = ...;
//   profiler.SetCustomCompletedCallback(thread_safe_callback);
//
//   profiler.Start();
//   // ... work being done on the target thread here ...
//   profiler.Stop();  // optional, stops collection before complete per params
//
// When all profiles are complete or the profiler is stopped, if the custom
// completed callback was set it will be called from the profiler thread with
// the completed profiles. If no callback was set, the profiles are stored
// internally and retrieved for UMA through
// GetPendingProfiles(). GetPendingProfiles() should never be called by other
// code; to retrieve profiles for in-process processing, set a completed
// callback.
class BASE_EXPORT StackSamplingProfiler {
 public:
  // Module represents the module (DLL or exe) corresponding to a stack frame.
  struct BASE_EXPORT Module {
    Module();
    Module(const void* base_address, const std::string& id,
           const FilePath& filename);
    ~Module();

    // Points to the base address of the module.
    const void* base_address;
    // An opaque binary string that uniquely identifies a particular program
    // version with high probability. This is parsed from headers of the loaded
    // module.
    // For binaries generated by GNU tools:
    //   Contents of the .note.gnu.build-id field.
    // On Windows:
    //   GUID + AGE in the debug image headers of a module.
    std::string id;
    // The filename of the module.
    FilePath filename;
  };

  // Frame represents an individual sampled stack frame with module information.
  struct BASE_EXPORT Frame {
    Frame();
    Frame(const void* instruction_pointer, int module_index);
    ~Frame();

    // The sampled instruction pointer within the function.
    const void* instruction_pointer;
    // Index of the module in the array of modules. We don't represent module
    // state directly here to save space.
    int module_index;
  };

  // Sample represents a set of stack frames.
  using Sample = std::vector<Frame>;

  // Profile represents a set of samples.
  struct BASE_EXPORT Profile {
    Profile();
    ~Profile();

    std::vector<Module> modules;
    std::vector<Sample> samples;
    // Duration of this profile.
    TimeDelta profile_duration;
    // Time between samples.
    TimeDelta sampling_period;
    // True if sample ordering is important and should be preserved if and when
    // this profile is compressed and processed.
    bool preserve_sample_ordering;
  };

  // NativeStackSampler abstracts the native implementation required to record a
  // stack sample for a given thread.
  class NativeStackSampler {
   public:
    virtual ~NativeStackSampler();

    // Create a stack sampler that records samples for |thread_handle|. Returns
    // null if this platform does not support stack sampling.
    static scoped_ptr<NativeStackSampler> Create(PlatformThreadId thread_id);

    // Notify the sampler that we're starting to record a new profile. This
    // function is called on the SamplingThread.
    virtual void ProfileRecordingStarting(Profile* profile) = 0;

    // Record a stack sample. This function is called on the SamplingThread.
    virtual void RecordStackSample(Sample* sample) = 0;

    // Notify the sampler that we've stopped recording the current profile. This
    // function is called on the SamplingThread.
    virtual void ProfileRecordingStopped() = 0;

   protected:
    NativeStackSampler();

   private:
    DISALLOW_COPY_AND_ASSIGN(NativeStackSampler);
  };

  // Represents parameters that configure the sampling.
  struct BASE_EXPORT SamplingParams {
    SamplingParams();

    // Time to delay before first samples are taken. Defaults to 0.
    TimeDelta initial_delay;
    // Number of sampling bursts to perform. Defaults to 1.
    int bursts;
    // Interval between sampling bursts. This is the desired duration from the
    // start of one burst to the start of the next burst. Defaults to 10s.
    TimeDelta burst_interval;
    // Number of samples to record per burst. Defaults to 300.
    int samples_per_burst;
    // Interval between samples during a sampling burst. This is the desired
    // duration from the start of one burst to the start of the next
    // burst. Defaults to 100ms.
    TimeDelta sampling_interval;
    // True if sample ordering is important and should be preserved if and when
    // this profile is compressed and processed. Defaults to false.
    bool preserve_sample_ordering;
  };

  StackSamplingProfiler(PlatformThreadId thread_id,
                        const SamplingParams& params);
  ~StackSamplingProfiler();

  // Initializes the profiler and starts sampling.
  void Start();
  // Stops the profiler and any ongoing sampling. Calling this function is
  // optional; if not invoked profiling will terminate when all the profiling
  // bursts specified in the SamplingParams are completed.
  void Stop();

  // Gets the pending profiles into *|profiles| and clears the internal
  // storage. This function is thread safe.
  //
  // ***This is intended for use only by UMA.*** Callers who want to process the
  // collected profiles should use SetCustomCompletedCallback.
  static void GetPendingProfiles(std::vector<Profile>* profiles);

  // By default, collected profiles are stored internally and can be retrieved
  // by GetPendingProfiles. If a callback is provided via this function,
  // however, it will be called with the collected profiles instead. Note that
  // this call to the callback occurs *on the profiler thread*.
  void SetCustomCompletedCallback(
      Callback<void(const std::vector<Profile>&)> callback);

 private:
  class SamplingThread;
  struct SamplingThreadDeleter {
    void operator() (SamplingThread* thread) const;
  };

  // The thread whose stack will be sampled.
  PlatformThreadId thread_id_;

  const SamplingParams params_;

  scoped_ptr<SamplingThread, SamplingThreadDeleter> sampling_thread_;
  scoped_ptr<NativeStackSampler> native_sampler_;

  Callback<void(const std::vector<Profile>&)> custom_completed_callback_;

  DISALLOW_COPY_AND_ASSIGN(StackSamplingProfiler);
};

// Defined to allow equality check of Samples.
BASE_EXPORT bool operator==(const StackSamplingProfiler::Frame& a,
                            const StackSamplingProfiler::Frame& b);
// Defined to allow ordering of Samples.
BASE_EXPORT bool operator<(const StackSamplingProfiler::Frame& a,
                           const StackSamplingProfiler::Frame& b);

}  // namespace base

#endif  // BASE_PROFILER_STACK_SAMPLING_PROFILER_H_
