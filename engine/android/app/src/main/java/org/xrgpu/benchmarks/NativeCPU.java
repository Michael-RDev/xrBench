package org.xrgpu.benchmarks;

/** CPU library intentionally has no Vulkan or OpenXR dependency. */
public final class NativeCPU {
    static { System.loadLibrary("xrbench_cpu"); }
    public static native String capabilities();
    public static native long create(String path);
    public static native float[] infer(long handle, byte[][] inputs, int[][] shapes, String[] types);
    public static native long residentCreate(byte[] a,byte[] b,int m,int k,int n,boolean candidate,String control);
    public static native void residentRun(long handle);
    public static native float[] residentRead(long handle);
    public static native String stats(long handle);
    public static native void destroy(long handle);
}
