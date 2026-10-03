/** 事件瞬时显式展示北京时间；纯业务日期不要调用此函数。 */
const formatter = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});

export function beijingTime(value: string | Date | number): string {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : formatter.format(date);
}

export function beijingInput(value: Date): string {
  return beijingTime(value).replace(' ', 'T').slice(0, 16);
}

export function fromBeijingInput(value: string): string {
  return new Date(`${value}:00+08:00`).toISOString();
}
