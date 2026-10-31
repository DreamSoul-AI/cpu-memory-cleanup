"use strict";
// 只拥有圆环的过程显示，不持有系统接口、报告或业务完成控制。
class CleanupEffect {
  constructor(render){this.render=render;this.phase=null;this.frame=null;this.latest=null;}
  track(value){if(value!=null&&Number.isFinite(value))this.latest=value;}
  start(value){
    this.stop();this.track(value);this.origin=this.latest||0;this.value=this.origin;
    this.phase="emptying";this.started=performance.now();this.ready=false;
    this.render(this.value,this.phase);this.frame=requestAnimationFrame(now=>this.tick(now));
  }
  finish(value){this.track(value);this.ready=true;}
  stop(){
    if(this.frame!=null)cancelAnimationFrame(this.frame);
    this.frame=null;this.phase=null;this.render(null,"idle");
  }
  tick(now){
    this.frame=null;if(!this.phase)return;
    if(this.phase==="emptying"){
      const p=Math.min(1,Math.max(0,(now-this.started)/3000));this.value=this.origin*(1-p);
      if(p===1)this.phase="waiting";
    }
    if(this.phase==="waiting"&&this.ready){
      if(this.latest==null){this.stop();return;}
      this.phase="returning";this.started=now;this.target=this.latest;
    }else if(this.phase==="returning"){
      const p=Math.min(1,Math.max(0,(now-this.started)/1200));this.value=this.target*(1-(1-p)**3);
      if(p===1){this.stop();return;}
    }
    this.render(this.value,this.phase);this.frame=requestAnimationFrame(t=>this.tick(t));
  }
}
