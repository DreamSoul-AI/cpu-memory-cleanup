"use strict";
// Position calculation and painting only. No samples, workflow, requests or timers.
class MemoryDisplay {
  constructor(render){this.render=render;}
  paint(value,phase){this.render(value,phase);}
  interpolate(from,to,progress){return from+(to-from)*progress;}
  follow(value,target,dt){return value+(target-value)*(1-Math.exp(-dt/100));}
}
