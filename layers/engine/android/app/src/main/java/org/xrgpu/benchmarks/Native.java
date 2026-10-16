package org.xrgpu.benchmarks;

public final class Native {
    static { System.loadLibrary("xrgpu_benchmarks"); }
    public static native String capabilities();
    public static native String scene(boolean reset, boolean frames);
    public static native long create(String path);
    public static native float[] infer(long handle, byte[][] inputs, int[][] shapes, String[] types);
    public static native String stats(long handle);
    public static native void destroy(long handle);
}
