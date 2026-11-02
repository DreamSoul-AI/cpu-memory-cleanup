"use strict";
// Visual workflow only. Raw snapshots and business reports belong to the application.
class CleanupFlow {
  constructor(render,refresh,reduced=()=>false){
    this.display=new MemoryDisplay(render);this.refresh=refresh;this.reduced=reduced;
    this.render=(value,phase)=>this.display.paint(value,phase);this.targetSample=null;
    this.actual=null;this.value=null;this.phase=null;this.frame=null;this.cycle=0;
    this.last=performance.now();this.suspended=false;this.gaps=[];
  }
  update(value,sample=null){
    if(value==null||!Number.isFinite(value))return;
    this.actual=value;this.latestSample=sample;
    if(this.phase==="returning"&&value!==this.target){
      this.origin=this.value;this.target=value;this.targetSample=sample;
      this.started=performance.now();this.duration=Math.max(0,this.deadline-this.started);
    }
    if(this.phase==="working"||this.phase==="refreshing")return;
    if(this.value==null||this.reduced()){
      this.value=value;if(!this.phase&&!this.suspended)this.render(value,"idle");
    }else if(this.phase!=="refreshing")this.schedule();
  }
  begin(){
    this.cycle++;if(this.frame!=null)cancelAnimationFrame(this.frame);this.frame=null;
    this.phase=this.suspended||this.reduced()?null:"working";
    if(this.phase)this.render(this.value,"working");
  }
  schedule(){if(this.frame==null&&!this.suspended){this.last=performance.now();this.frame=requestAnimationFrame(time=>this.tick(time));}}
  next(){if(this.frame==null&&!this.suspended)this.frame=requestAnimationFrame(time=>this.tick(time));}
  start(value=this.value){
    this.cycle++;if(this.frame!=null)cancelAnimationFrame(this.frame);this.frame=null;
    if(this.suspended||this.reduced()||value==null||!Number.isFinite(value)){this.phase=null;return;}
    this.origin=value;this.phase="emptying";this.started=performance.now();this.schedule();
  }
  stop(){this.cycle++;this.phase=null;this.schedule();}
  suspend(){this.cycle++;this.phase=null;this.suspended=true;if(this.frame!=null)cancelAnimationFrame(this.frame);this.frame=null;}
  resume(){this.suspended=false;this.schedule();}
  stats(){
    const sorted=[...this.gaps].sort((a,b)=>a-b);
    return {phase:this.phase,value:this.value,actual:this.actual,target:this.target??null,targetSample:this.targetSample,latestSample:this.latestSample??null,frames:sorted.length,p95_gap_ms:sorted.length?sorted[Math.round((sorted.length-1)*.95)]:null,max_gap_ms:sorted.at(-1)??null};
  }
  tick(now){
    this.frame=null;if(this.suspended)return;
    const dt=Math.max(0,now-this.last);this.last=now;
    this.gaps.push(dt);if(this.gaps.length>240)this.gaps.shift();
    if(this.phase==="working")return;
    if(this.phase==="emptying"){
      const progress=Math.min(1,Math.max(0,(now-this.started)/750));this.value=this.origin*(1-progress);
      if(progress===1){
        this.phase="refreshing";this.render(0,this.phase);
        const cycle=this.cycle;
        Promise.resolve().then(()=>this.cycle===cycle?this.refresh():null).then(value=>{
          if(this.cycle!==cycle||this.phase!=="refreshing")return;
          if(value==null||!Number.isFinite(value)){this.stop();return;}
          this.target=value;this.targetSample=this.latestSample;this.origin=0;this.phase="returning";this.started=performance.now();this.duration=300;this.deadline=this.started+300;this.schedule();
        }).catch(()=>{if(this.cycle===cycle)this.stop();});
        return;
      }
    }else if(this.phase==="returning"){
      const progress=this.duration===0?1:Math.min(1,Math.max(0,(now-this.started)/this.duration));this.value=this.display.interpolate(this.origin,this.target,progress);
      if(progress===1)this.phase=null;
    }else if(this.actual!=null){
      // Preserve the current position when a new observation arrives or an effect ends.
      this.value=this.reduced()?this.actual:this.display.follow(this.value,this.actual,dt);
      if(Math.abs(this.actual-this.value)<.01)this.value=this.actual;
    }
    this.render(this.value,this.phase||"idle");
    if(this.phase||this.actual!=null&&this.value!==this.actual)this.next();
  }
}
