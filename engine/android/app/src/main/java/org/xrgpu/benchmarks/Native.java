package org.xrgpu.benchmarks;

public final class Native {
    static { System.loadLibrary("xrgpu_benchmarks"); }
    public static native String capabilities();
    public static native long residentCreate(byte[] a,byte[] b,int m,int k,int n,boolean candidate,boolean detailed,String control);
    public static native void residentRun(long handle);
    public static native float[] residentRead(long handle);
    public static native String residentStats(long handle);
    public static native void residentDestroy(long handle);
    public static native String calibrate(java.util.function.BooleanSupplier canceled);
    public static native String scene(boolean reset, boolean frames);
    public static native long create(String path,String variant,boolean detailed,String control);
    public static native float[] infer(long handle, byte[][] inputs, int[][] shapes, String[] types);
    public static native String stats(long handle);
    public static native void destroy(long handle);
}
