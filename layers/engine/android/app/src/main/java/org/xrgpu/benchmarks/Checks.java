package org.xrgpu.benchmarks;

import org.json.*;

public final class Checks {
    public static JSONObject compare(float[] actual,float[] expected,JSONArray shape,JSONArray expectedShape,double atol,double rtol) throws Exception {
        if(actual.length!=expected.length||!shape.toString().equals(expectedShape.toString()))
            return new JSONObject().put("passed",false).put("reason","output shape mismatch");
        double max=0,ratio=0;
        for(int i=0;i<actual.length;i++) {
            if(!Float.isFinite(actual[i])||!Float.isFinite(expected[i]))return new JSONObject().put("passed",false).put("reason","non-finite output");
            double error=Math.abs((double)actual[i]-expected[i]);max=Math.max(max,error);
            ratio=Math.max(ratio,error/(atol+rtol*Math.abs(expected[i])));
        }
        return new JSONObject().put("passed",ratio<=1).put("max_abs_error",max).put("max_tolerance_ratio",ratio).put("atol",atol).put("rtol",rtol);
    }
    public static void identity(String id) {
        if(id==null||!id.matches("run-[0-9a-f]{32}"))throw new IllegalArgumentException("Invalid run identity");
    }
}
