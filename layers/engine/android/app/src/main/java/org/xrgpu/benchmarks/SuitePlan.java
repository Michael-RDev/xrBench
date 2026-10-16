package org.xrgpu.benchmarks;

import org.json.*;

/** Scheduling shared by the Android runner and host-side contract tests. */
public final class SuitePlan {
    public static final class SessionFailure extends Exception {
        public SessionFailure(String message){super(message);}
        public SessionFailure(String message,Throwable cause){super(message,cause);}
    }
    public interface Worker {
        void execute(JSONObject spec,int index) throws Exception;
        void failed(JSONObject spec,Exception error) throws Exception;
        default void finished(JSONObject spec) throws Exception {}
    }
    public final JSONArray cases;
    public final String preset;
    public final boolean xr;
    public SuitePlan(JSONArray cases,JSONArray requested,String preset,boolean xr) throws Exception {
        if(!preset.equals("smoke")&&!preset.equals("standard")&&!preset.equals("sustained"))throw new IllegalArgumentException("Unknown preset");
        if(cases.length()==0||requested.length()!=cases.length())throw new IllegalArgumentException("Run requires the complete layer catalog");
        for(int i=0;i<cases.length();i++)if(!requested.getString(i).equals(cases.getJSONObject(i).getString("id")))throw new IllegalArgumentException("Run requires catalog order without duplicates");
        this.cases=cases;this.preset=preset;this.xr=xr;
    }
    public int warmups(){return preset.equals("smoke")?5:20;}
    public int samples(){return preset.equals("smoke")?20:100;}
    public int pairs(){return preset.equals("smoke")?2:4;}
    public int windowSeconds(){return preset.equals("smoke")?5:preset.equals("standard")?15:75;}
    public long plannedWindowMs(){return xr?(long)cases.length()*pairs()*2*windowSeconds()*1000:0;}
    public long deadlineMs(){return Math.max(1800000L,plannedWindowMs()*5/4+300000L);}
    public void runCases(Worker worker) throws Exception {
        for(int i=0;i<cases.length();i++){
            JSONObject spec=cases.getJSONObject(i);
            try{worker.execute(spec,i);}
            catch(InterruptedException|SessionFailure error){throw error;}
            catch(Exception error){worker.failed(spec,error);}
            worker.finished(spec);
        }
    }
}
