import test from 'node:test';
import assert from 'node:assert/strict';
import { formatFen, formatYuan, formatYuanText, maskCard } from '../src/utils/money.ts';

test('金额统一千分位、两位小数', () => {
  assert.equal(formatFen(5_000_000), '¥50,000.00');
  assert.equal(formatFen(5), '¥0.05');
  assert.equal(formatFen(-12_000), '−¥120.00');
  assert.equal(formatYuan(7219.12), '¥7,219.12');
  assert.equal(formatYuan(0.1 + 0.2), '¥0.30');
  assert.equal(formatYuan(1999.995 / 100 * 100), '¥2,000.00');
});

test('未知金额不显示成 0', () => {
  assert.equal(formatFen(null), '—');
  assert.equal(formatYuan(undefined), '—');
  assert.equal(formatYuan(Number.NaN), '—');
});

test('用户填写的金额文本', () => {
  assert.equal(formatYuanText('1200.5'), '¥1,200.50');
  assert.equal(formatYuanText('300'), '¥300.00');
  assert.equal(formatYuanText('待定'), '¥待定');
});

test('卡号分组显示', () => {
  assert.equal(maskCard('6222****8842'), '6222 **** 8842');
  assert.equal(maskCard('6222000011118842'), '6222 **** 8842');
  assert.equal(maskCard(null), '—');
});
