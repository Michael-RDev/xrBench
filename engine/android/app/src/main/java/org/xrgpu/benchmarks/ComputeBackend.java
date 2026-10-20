package org.xrgpu.benchmarks;
import org.json.*;

/** Execution boundary shared by the scheduler and correctness checks. */
public interface ComputeBackend extends AutoCloseable {
    float[] infer();
    float[] output(float[] result);
    JSONObject stats() throws Exception;
    void close();
    static ComputeBackend open(JSONObject config,JSONObject spec,String path,String variant,byte[][] inputs,int[][] shapes,String[] types) throws Exception {
        String name=config.optString("backend","executorch_vulkan");boolean trace=!config.optString("profiling","trace").equals("latency");
        if(name.equals("executorch_cpu")){
            long handle=NativeCPU.create(path);
            return new ComputeBackend(){
                public float[] infer(){return NativeCPU.infer(handle,inputs,shapes,types);}
                public float[] output(float[] actual){return actual;}
                public JSONObject stats() throws Exception{return new JSONObject(NativeCPU.stats(handle));}
                public void close(){NativeCPU.destroy(handle);}
            };
        }
        if(name.equals("cpu_resident")){
            if(!spec.getString("operation").equals("matmul")||inputs.length!=2||shapes[0].length!=2||shapes[1].length!=2
                ||shapes[0][1]!=shapes[1][0]||!types[0].equals("float32")||!types[1].equals("float32"))throw new IllegalArgumentException("Resident CPU requires 2D FP32 matmul");
            long handle=NativeCPU.residentCreate(inputs[0],inputs[1],shapes[0][0],shapes[0][1],shapes[1][1],variant.equals("candidate"),config.optString("validation_control","none"));
            return new ComputeBackend(){
                public float[] infer(){NativeCPU.residentRun(handle);return null;}
                public float[] output(float[] actual){return NativeCPU.residentRead(handle);}
                public JSONObject stats() throws Exception{return new JSONObject(NativeCPU.stats(handle));}
                public void close(){NativeCPU.destroy(handle);}
            };
        }
        if(name.equals("executorch_vulkan")){
            long handle=Native.create(path,variant,trace,config.optString("validation_control","none"));
            return new ComputeBackend(){
                public float[] infer(){return Native.infer(handle,inputs,shapes,types);}
                public float[] output(float[] actual){return actual;}
                public JSONObject stats() throws Exception{return new JSONObject(Native.stats(handle));}
                public void close(){Native.destroy(handle);}
            };
        }
        if(name.equals("vulkan_resident")){
            if(!spec.getString("operation").equals("matmul")||inputs.length!=2||shapes[0].length!=2||shapes[1].length!=2
                ||shapes[0][1]!=shapes[1][0]||!types[0].equals("float32")||!types[1].equals("float32"))throw new IllegalArgumentException("Resident backend requires 2D FP32 matmul");
            long handle=Native.residentCreate(inputs[0],inputs[1],shapes[0][0],shapes[0][1],shapes[1][1],variant.equals("candidate"),trace,config.optString("validation_control","none"));
            return new ComputeBackend(){
                public float[] infer(){Native.residentRun(handle);return null;}
                public float[] output(float[] actual){return Native.residentRead(handle);}
                public JSONObject stats() throws Exception{return new JSONObject(Native.residentStats(handle));}
                public void close(){Native.residentDestroy(handle);}
            };
        }
        throw new IllegalArgumentException("Unknown backend");
    }
}
