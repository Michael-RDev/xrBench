package org.xrgpu.benchmarks;

final class NativeOperators {
    static { System.loadLibrary("xrbench_native_operators"); }
    static native String probe();
    static native void run(String assets,String configuration,String output,String device);
}
