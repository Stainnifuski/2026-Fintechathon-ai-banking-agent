import {test} from 'node:test';
import assert from 'node:assert/strict';
import {isSessionAuthenticationFailure} from '../src/utils/sessionAuthentication.ts';

test('wrong verification proofs and security locks preserve the login session', () => {
  for (const code of ['SMS_INVALID', 'TOTP_INVALID', 'TOTP_REPLAY', 'INVALID_CREDENTIALS', 'SECURITY_LOCKED']) {
    assert.equal(isSessionAuthenticationFailure({response: {status: 401, data: {detail: {code}}}}), false);
  }
});

test('normalized and legacy login-session failures require login again', () => {
  for (const detail of [{code: 'AUTH_REQUIRED'}, {code: 'INVALID_TOKEN'}, {code: 'TOKEN_EXPIRED'}, '无效的认证令牌']) {
    assert.equal(isSessionAuthenticationFailure({response: {status: 401, data: {detail}}}), true);
  }
});

test('business rejection and malformed errors do not expire a session', () => {
  for (const error of [undefined, {}, {response: {status: 403}}, {response: {status: 429, data: {detail: {code: 'SECURITY_LOCKED'}}}}]) {
    assert.equal(isSessionAuthenticationFailure(error), false);
  }
});
