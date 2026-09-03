import { DisInvalidArgumentError } from './chunk-MJO7IJZC.js';
import * as OTPAuth from 'otpauth';

var TOTP_ALGORITHM = "SHA1";
var TOTP_DIGITS = 6;
var TOTP_PERIOD_SECONDS = 30;
var TOTP_SECRET_SIZE = 20;
function generateTotpSecret() {
  return new OTPAuth.Secret({ size: TOTP_SECRET_SIZE }).base32;
}
function buildTotp(params) {
  return new OTPAuth.TOTP({
    issuer: params.issuer,
    label: params.label,
    algorithm: TOTP_ALGORITHM,
    digits: TOTP_DIGITS,
    period: TOTP_PERIOD_SECONDS,
    secret: OTPAuth.Secret.fromBase32(params.secret.replace(/\s/g, ""))
  });
}
function buildTotpUri(params) {
  if (!params.secret) {
    throw new DisInvalidArgumentError("TOTP secret is required");
  }
  return buildTotp(params).toString();
}
function generateTotpCode(secret, options = {}) {
  let parsedSecret;
  try {
    parsedSecret = OTPAuth.Secret.fromBase32(secret.replace(/\s/g, ""));
  } catch {
    throw new DisInvalidArgumentError("TOTP secret is not valid base32");
  }
  const totp = new OTPAuth.TOTP({
    algorithm: options.algorithm ?? TOTP_ALGORITHM,
    digits: options.digits ?? TOTP_DIGITS,
    period: options.period ?? TOTP_PERIOD_SECONDS,
    secret: parsedSecret
  });
  return totp.generate(
    options.timestamp !== void 0 ? { timestamp: options.timestamp } : void 0
  );
}
function buildTotpUriWithOptions(params, options = {}) {
  if (!params.secret) {
    throw new DisInvalidArgumentError("TOTP secret is required");
  }
  return new OTPAuth.TOTP({
    issuer: params.issuer,
    label: params.label,
    algorithm: options.algorithm ?? TOTP_ALGORITHM,
    digits: options.digits ?? TOTP_DIGITS,
    period: options.period ?? TOTP_PERIOD_SECONDS,
    secret: OTPAuth.Secret.fromBase32(params.secret.replace(/\s/g, ""))
  }).toString();
}
function verifyTotpCode(secret, code, window = 1) {
  try {
    const totp = new OTPAuth.TOTP({
      algorithm: TOTP_ALGORITHM,
      digits: TOTP_DIGITS,
      period: TOTP_PERIOD_SECONDS,
      secret: OTPAuth.Secret.fromBase32(secret.replace(/\s/g, ""))
    });
    return totp.validate({ token: code.replace(/\s/g, ""), window }) !== null;
  } catch {
    return false;
  }
}

export { TOTP_ALGORITHM, TOTP_DIGITS, TOTP_PERIOD_SECONDS, TOTP_SECRET_SIZE, buildTotpUri, buildTotpUriWithOptions, generateTotpCode, generateTotpSecret, verifyTotpCode };
//# sourceMappingURL=chunk-OA5ARYJM.js.map
//# sourceMappingURL=chunk-OA5ARYJM.js.map