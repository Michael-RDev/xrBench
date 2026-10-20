package org.xrgpu.benchmarks;

import java.util.*;
import java.util.function.LongSupplier;
import org.json.*;

/** Exclusive stage accounting on one monotonic clock, through the snapshot instant. */
public final class TimingLedger {
    final LongSupplier clock;final long started;long last;String stage="preparation";
    final Map<String,Long> totals=new LinkedHashMap<>();
    public TimingLedger(){this(System::nanoTime);}
    public TimingLedger(LongSupplier clock){this.clock=clock;started=last=clock.getAsLong();
        for(String name:new String[]{"preparation","calibration","warmup","measurement","validation","pacing","reporting","other"})totals.put(name,0L);
    }
    void account(){long now=clock.getAsLong();totals.merge(stage,Math.max(0,now-last),Long::sum);last=now;}
    public Scope enter(String name){account();String previous=stage;stage=name;return new Scope(previous);}
    public final class Scope implements AutoCloseable {
        final String previous;boolean closed;
        Scope(String previous){this.previous=previous;}
        public void close(){if(!closed){account();stage=previous;closed=true;}}
    }
    public JSONObject snapshot() throws JSONException {
        account();JSONObject stages=new JSONObject();for(Map.Entry<String,Long> e:totals.entrySet())stages.put(e.getKey(),e.getValue()/1e6);
        return new JSONObject().put("version",1).put("session_ms",(last-started)/1e6).put("stages_ms",stages)
            .put("scope","Exclusive headset wall time through this snapshot; excludes publishing this snapshot. Nested inference and GPU timings are separate.");
    }
}
