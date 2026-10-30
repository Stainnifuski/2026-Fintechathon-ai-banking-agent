export type HandoffCandidate = {
  meta:{key:string;session:string;ready?:boolean;finished?:boolean;released?:unknown};
  ended?:boolean;uncertain?:boolean;completed?:boolean;
};

export function selectHandoffCandidate<T extends HandoffCandidate>(items:Iterable<T>,session:string,focusedKey:string|null):T|undefined{
  const available=[...items].filter(item=>item.meta.session===session&&item.meta.ready!==false
    &&!item.ended&&!item.uncertain&&!item.meta.finished&&!item.completed&&!item.meta.released);
  return available.find(item=>item.meta.key===focusedKey)??available[available.length-1];
}
