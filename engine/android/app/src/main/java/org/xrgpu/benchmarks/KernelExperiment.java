package org.xrgpu.benchmarks;

import java.util.*;

/** Balanced randomized order; the pair, not an individual sample, is the analysis unit. */
public final class KernelExperiment {
    public static int[] order(int pairs,long seed) {
        if(pairs<2||pairs>40||pairs%2!=0)throw new IllegalArgumentException("Use an even number of pairs between 2 and 40");
        List<Integer> order=new ArrayList<>();for(int i=0;i<pairs;i++)order.add(i%2);
        Collections.shuffle(order,new Random(seed));int[] result=new int[pairs];
        for(int i=0;i<pairs;i++)result[i]=order.get(i);return result;
    }
}
