package org.xrgpu.benchmarks;
import org.json.*;
public final class TimingChecks {
    static void require(boolean value){if(!value)throw new AssertionError();}
    public static void main(String[] args) throws Exception {
        long[] time={0};TimingLedger ledger=new TimingLedger(()->time[0]);
        time[0]=1000000;
        try(TimingLedger.Scope measurement=ledger.enter("measurement")){
            time[0]+=2000000;
            try(TimingLedger.Scope validation=ledger.enter("validation")){time[0]+=3000000;}
            time[0]+=4000000;
        }
        JSONObject snapshot=ledger.snapshot(),stages=snapshot.getJSONObject("stages_ms");
        require(stages.getDouble("preparation")==1&&stages.getDouble("measurement")==6&&stages.getDouble("validation")==3);
        require(snapshot.getDouble("session_ms")==10);
        time[0]+=1000000;
        require(ledger.snapshot().getDouble("session_ms")==11);
        System.out.println("Timing ledger checks passed");
    }
}
