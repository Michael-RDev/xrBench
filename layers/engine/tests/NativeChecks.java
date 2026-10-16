package org.xrgpu.benchmarks;
import org.json.JSONArray;

public final class NativeChecks {
    static void require(boolean ok){if(!ok)throw new AssertionError();}
    public static void main(String[] args) throws Exception {
        JSONArray shape=new JSONArray("[1,2]");
        require(Checks.compare(new float[]{1,2},new float[]{1,2},shape,shape,1e-4,1e-4).getBoolean("passed"));
        require(!Checks.compare(new float[]{1,2},new float[]{1,2},new JSONArray("[2,1]"),shape,1e-4,1e-4).getBoolean("passed"));
        require(!Checks.compare(new float[]{1,Float.NaN},new float[]{1,2},shape,shape,1e-4,1e-4).getBoolean("passed"));
        require(!Checks.compare(new float[]{1,3},new float[]{1,2},shape,shape,1e-4,1e-4).getBoolean("passed"));
        Checks.identity("run-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        boolean rejected=false;try{Checks.identity("../../escape");}catch(IllegalArgumentException e){rejected=true;}require(rejected);
        System.out.println("Native Android correctness and identity checks passed");
    }
}
