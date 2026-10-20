package org.xrgpu.benchmarks;
import org.json.*;
public final class CalibrationChecks {
    static void require(boolean value){if(!value)throw new AssertionError();}
    public static void main(String[] args) throws Exception {
        JSONObject caps=new JSONObject().put("gpu","reference GPU").put("driver",12).put("runner_revision","runner-a");
        JSONObject identity=CalibrationPolicy.identity(caps,"os-a","isolated",0,false);
        JSONObject profile=new JSONObject().put("identity",identity.toString()).put("version",1).put("available",true)
            .put("compute_gflops",100).put("memory_gbps",10).put("dispatch_ms",.01).put("wall_ms",12000).put("scratch_bytes",32*1024*1024);
        require(CalibrationPolicy.compatible(profile,identity));
        for(String key:new String[]{"gpu","driver","os","precision","mode","runner","thermal_start","charging_start","version"}){
            JSONObject changed=new JSONObject(identity.toString()).put(key,"changed");require(!CalibrationPolicy.compatible(profile,changed));
        }
        for(String key:new String[]{"compute_gflops","memory_gbps","dispatch_ms"}){
            JSONObject changed=new JSONObject(profile.toString()).put(key,0);require(!CalibrationPolicy.compatible(changed,identity));
        }
        require(!CalibrationPolicy.compatible(new JSONObject(profile.toString()).put("wall_ms",15001),identity));
        require(!CalibrationPolicy.compatible(new JSONObject(profile.toString()).put("scratch_bytes",64L*1024*1024+1),identity));
        require(!CalibrationPolicy.compatible(new JSONObject(profile.toString()).put("available",false),identity));
        require(!CalibrationPolicy.compatible(new JSONObject(),identity));
        System.out.println("Calibration policy checks passed");
    }
}
