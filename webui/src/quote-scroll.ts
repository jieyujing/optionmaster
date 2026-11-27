/** Normalized distance from the strike: zero is inward, one is outermost. */
export type QuoteSide="CALL"|"PUT";
export const clampUnit=(value:number)=>Number.isFinite(value)?Math.max(0,Math.min(1,value)):0;
export const extent=(size:number,viewport:number)=>Math.max(0,size-viewport);
export function outwardFraction(side:QuoteSide,scrollLeft:number,max:number):number{
  if(max<=0)return 0;
  const x=clampUnit(scrollLeft/max);
  return side==="CALL"?1-x:x;
}
export function mirrorLeft(side:QuoteSide,fraction:number,max:number):number{
  const f=clampUnit(fraction);
  return Math.round(Math.max(0,max)*(side==="CALL"?1-f:f));
}
export function alignedTop(value:number,...maxima:number[]):number{
  return Math.max(0,Math.min(Math.round(value),...maxima.map(m=>Math.max(0,m))));
}
