package org.xrgpu.benchmarks;
import java.util.Arrays;
public final class KernelExperimentChecks {
    public static void main(String[] args) {
        for(int n:new int[]{6,10,40})for(int seed=0;seed<100;seed++){
            int[] a=KernelExperiment.order(n,seed),b=KernelExperiment.order(n,seed);
            if(!Arrays.equals(a,b)||Arrays.stream(a).sum()!=n/2)throw new AssertionError("Unbalanced or irreproducible order");
            for(int arm:a)if(arm!=0&&arm!=1)throw new AssertionError("Unknown arm");
        }
        for(int n:new int[]{0,3,41}){
            try{KernelExperiment.order(n,0);throw new AssertionError("Invalid count accepted");}
            catch(IllegalArgumentException expected){}
        }
        System.out.println("Experiment scheduling checks passed");
    }
}
