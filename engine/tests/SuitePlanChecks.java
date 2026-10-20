package org.xrgpu.benchmarks;
import org.json.*;
import java.nio.file.*;
import java.util.*;

public final class SuitePlanChecks {
    static void require(boolean ok){if(!ok)throw new AssertionError();}
    public static void main(String[] args) throws Exception {
        JSONArray cases=new JSONObject(Files.readString(Path.of(args[0]))).getJSONArray("cases"),ids=new JSONArray();
        for(int i=0;i<cases.length();i++)ids.put(cases.getJSONObject(i).getString("id"));
        require(ids.length()==63);
        for(String preset:new String[]{"smoke","standard","sustained"})for(boolean xr:new boolean[]{false,true}){
            SuitePlan plan=new SuitePlan(cases,ids,preset,xr);
            List<String> seen=new ArrayList<>(),finished=new ArrayList<>();Set<String> failed=new HashSet<>(),skipped=new HashSet<>();
            plan.runCases(new SuitePlan.Worker(){
                public void execute(JSONObject spec,int index) throws Exception {
                    seen.add(spec.getString("id"));
                    if(index==1)throw new IllegalStateException("Injected correctness failure");
                    if(index==2)skipped.add(spec.getString("id"));
                }
                public void failed(JSONObject spec,Exception error) throws Exception {failed.add(spec.getString("id"));}
                public void finished(JSONObject spec) throws Exception {finished.add(spec.getString("id"));}
            });
            require(seen.equals(finished)&&seen.size()==63&&new HashSet<>(seen).size()==63);
            for(int i=0;i<63;i++)require(seen.get(i).equals(ids.getString(i)));
            require(failed.size()==1&&skipped.size()==1);
            require(plan.warmups()==(preset.equals("smoke")?5:20));
            require(plan.samples()==(preset.equals("smoke")?20:100));
            long expected=preset.equals("smoke")?1875000:preset.equals("standard")?9750000:47550000;
            require(plan.deadlineMs()==(xr?expected:1800000));
            require(plan.plannedWindowMs()==(xr?(preset.equals("smoke")?1260000:preset.equals("standard")?7560000:37800000):0));
        }
        for(boolean cancellation:new boolean[]{false,true}){
            List<String> visited=new ArrayList<>();
            try{
                new SuitePlan(cases,ids,"smoke",true).runCases(new SuitePlan.Worker(){
                    public void execute(JSONObject spec,int index) throws Exception {
                        visited.add(spec.getString("id"));if(index==2){if(cancellation)throw new InterruptedException("Stop");throw new SuitePlan.SessionFailure("Focus lost");}
                    }
                    public void failed(JSONObject spec,Exception error){throw new AssertionError("Session interruption must not become a case failure");}
                });
                throw new AssertionError("Expected session stop");
            }catch(InterruptedException|SuitePlan.SessionFailure expected){require(visited.size()==3);}
        }
        boolean rejected=false;
        SuitePlan subset=new SuitePlan(cases,new JSONArray().put(ids.getString(0)).put(ids.getString(3)),"smoke",false);
        require(subset.cases.length()==2&&subset.cases.getJSONObject(1).getString("id").equals(ids.getString(3)));
        try{new SuitePlan(cases,new JSONArray(),"smoke",false);}catch(IllegalArgumentException expected){rejected=true;}
        require(rejected);JSONArray reordered=new JSONArray(ids.toString());reordered.put(1,ids.getString(0));rejected=false;
        try{new SuitePlan(cases,reordered,"smoke",false);}catch(IllegalArgumentException expected){rejected=true;}
        require(rejected);
        System.out.println("Full-suite scheduling checks passed");
    }
}
