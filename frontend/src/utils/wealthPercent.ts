/** Exact decimal check; binary multiplication must not reject 57% or 58%. */
export function isIntegerPercentageRatio(text:string):boolean {
  return /^(?:0(?:\.\d{1,2}0*)?|1(?:\.0*)?)$/.test(text);
}
