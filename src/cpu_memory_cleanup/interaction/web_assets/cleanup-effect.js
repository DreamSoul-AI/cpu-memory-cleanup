"use strict";
// 只拥有圆环的过程显示，不持有系统接口、报告或业务完成控制。
class CleanupEffect {
  constructor(render){this.render=render;this.phase=null;this.frame=null;this.latest=null;}
  track(value,key=null){
    if(value==null||!Number.isFinite(value))return;
    const now=performance.now(),fresh=key==null||key!==this.lastKey;
    this.latest=value;if(key!=null)this.lastKey=key;
    if(this.phase&&this.ready&&fresh){
      this.readings.push({at:now,value});this.readings=this.readings.slice(-3);
    }
    if(this.phase==="returning"&&value!==this.target){
      this.from=this.value;this.target=value;this.segmentAt=now;
    }
  }
  start(value){
    this.stop();this.track(value);this.origin=this.latest||0;this.value=this.origin;
    this.phase="emptying";this.started=performance.now();this.ready=false;
    this.readings=[];this.lastKey=null;this.settleReason=null;
    this.render(this.value,this.phase);this.frame=requestAnimationFrame(now=>this.tick(now));
  }
  finish(value){
    if(!this.phase||this.ready)return;
    this.track(value);this.ready=true;this.finishedAt=performance.now();this.readings=[];
  }
  stop(){
    if(this.frame!=null)cancelAnimationFrame(this.frame);
    this.frame=null;this.phase=null;this.ready=false;this.render(null,"idle");
  }
  tick(now){
    this.frame=null;if(!this.phase)return;
    if(this.phase==="emptying"){
      const p=Math.min(1,Math.max(0,(now-this.started)/3000));this.value=this.origin*(1-p);
      if(p===1)this.phase=this.ready?"observing":"waiting";
    }
    if(this.phase==="waiting"&&this.ready)this.phase="observing";
    if(this.phase==="observing"){
      const elapsed=now-this.finishedAt,values=this.readings.map(r=>r.value);
      const quiet=values.length===3&&this.readings[2].at-this.readings[0].at>=2000
        &&Math.max(...values)-Math.min(...values)<=.75;
      if(elapsed>=8000&&!this.readings.length){this.settleReason="no_new_reading";this.stop();return;}
      if(elapsed>=3000&&(quiet||elapsed>=8000)){
        this.settleReason=quiet?"quiet_window":"time_limit";
        this.phase="returning";this.target=this.latest;this.from=0;this.segmentAt=now;this.returnUntil=now+1200;
      }
    }
    if(this.phase==="returning"){
      const p=Math.min(1,Math.max(0,(now-this.segmentAt)/Math.max(1,this.returnUntil-this.segmentAt)));
      this.value=this.from+(this.target-this.from)*(1-(1-p)**3);
      if(now>=this.returnUntil){this.stop();return;}
    }
    this.render(this.value,this.phase);this.frame=requestAnimationFrame(t=>this.tick(t));
  }
}
