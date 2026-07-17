// Color-grading hue is stored in degrees on the -180..180 wheels. The shader's
// hsvToRgb takes hue in TURNS (fract(h) * 6), so the conversion divides by the
// full circle, not the half: dividing by 180 once sent 60° in as 0.333 turns
// (pure green) instead of 0.167 (yellow), and collapsed +90 and -90 onto the
// same colour because fract(-0.5) === fract(0.5). This is the single source for
// that mapping — the render params and the panel swatch derive from it.

export function gradingHueToTurns(deg: number): number {
  return deg / 360;
}

export function gradingHueDeg(deg: number): number {
  return ((deg % 360) + 360) % 360;
}
