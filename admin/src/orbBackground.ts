const ORB_COUNT = 11;
const ORB_SPEED_PX_PER_SECOND = 6;

type Orb = {
  x: number;
  y: number;
  radius: number;
  angle: number;
  targetAngle: number;
  turnAfter: number;
  hue: number;
  hueSpeed: number;
  saturation: number;
  lightness: number;
};

const randomBetween = (minimum: number, maximum: number): number =>
  minimum + Math.random() * (maximum - minimum);

function createOrb(width: number, height: number): Orb {
  const shortestSide = Math.max(320, Math.min(width, height));
  const angle = randomBetween(0, Math.PI * 2);
  return {
    x: randomBetween(0, width),
    y: randomBetween(0, height),
    radius: randomBetween(shortestSide * 0.09, shortestSide * 0.24),
    angle,
    targetAngle: angle,
    turnAfter: randomBetween(2.5, 7),
    hue: randomBetween(0, 360),
    hueSpeed: randomBetween(3, 9) * (Math.random() > 0.5 ? 1 : -1),
    saturation: randomBetween(66, 88),
    lightness: randomBetween(54, 68),
  };
}

function shortestAngleDifference(from: number, to: number): number {
  return Math.atan2(Math.sin(to - from), Math.cos(to - from));
}

function drawOrb(context: CanvasRenderingContext2D, orb: Orb): void {
  const color = `${orb.hue.toFixed(2)} ${orb.saturation.toFixed(1)}% ${orb.lightness.toFixed(1)}%`;
  const glow = context.createRadialGradient(
    orb.x,
    orb.y,
    orb.radius * 0.05,
    orb.x,
    orb.y,
    orb.radius,
  );
  glow.addColorStop(0, `hsl(${color} / 0.025)`);
  glow.addColorStop(0.58, `hsl(${color} / 0.05)`);
  glow.addColorStop(0.8, `hsl(${color} / 0.13)`);
  glow.addColorStop(0.93, `hsl(${color} / 0.2)`);
  glow.addColorStop(1, `hsl(${color} / 0)`);

  context.save();
  context.globalCompositeOperation = "screen";
  context.filter = `blur(${Math.max(10, orb.radius * 0.07)}px)`;
  context.fillStyle = glow;
  context.beginPath();
  context.arc(orb.x, orb.y, orb.radius, 0, Math.PI * 2);
  context.fill();

  context.filter = `blur(${Math.max(7, orb.radius * 0.045)}px)`;
  context.strokeStyle = `hsl(${color} / 0.28)`;
  context.lineWidth = Math.max(1.5, orb.radius * 0.012);
  context.shadowBlur = Math.max(14, orb.radius * 0.16);
  context.shadowColor = `hsl(${color} / 0.32)`;
  context.beginPath();
  context.arc(orb.x, orb.y, orb.radius * 0.88, 0, Math.PI * 2);
  context.stroke();
  context.restore();
}

export function startOrbBackground(canvas: HTMLCanvasElement): void {
  const context = canvas.getContext("2d");
  if (!context) return;

  const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
  let width = window.innerWidth;
  let height = window.innerHeight;
  let orbs: Orb[] = [];
  let animationFrame = 0;
  let lastFrameTime = performance.now();

  const render = (): void => {
    context.clearRect(0, 0, width, height);
    for (const orb of orbs) drawOrb(context, orb);
  };

  const resize = (): void => {
    const previousWidth = width;
    const previousHeight = height;
    width = window.innerWidth;
    height = window.innerHeight;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);

    if (orbs.length === 0) {
      orbs = Array.from({ length: ORB_COUNT }, () => createOrb(width, height));
    } else {
      const scaleX = previousWidth > 0 ? width / previousWidth : 1;
      const scaleY = previousHeight > 0 ? height / previousHeight : 1;
      for (const orb of orbs) {
        orb.x *= scaleX;
        orb.y *= scaleY;
      }
    }
    render();
  };

  const update = (elapsedSeconds: number): void => {
    for (const orb of orbs) {
      orb.turnAfter -= elapsedSeconds;
      if (orb.turnAfter <= 0) {
        orb.targetAngle = randomBetween(0, Math.PI * 2);
        orb.turnAfter = randomBetween(2.5, 7);
      }

      const turnAmount = shortestAngleDifference(orb.angle, orb.targetAngle);
      orb.angle += turnAmount * Math.min(1, elapsedSeconds * 0.55);
      orb.x += Math.cos(orb.angle) * ORB_SPEED_PX_PER_SECOND * elapsedSeconds;
      orb.y += Math.sin(orb.angle) * ORB_SPEED_PX_PER_SECOND * elapsedSeconds;
      orb.hue = (orb.hue + orb.hueSpeed * elapsedSeconds + 360) % 360;

      const boundary = orb.radius * 0.35;
      if (orb.x < -boundary || orb.x > width + boundary) {
        orb.x = Math.min(width + boundary, Math.max(-boundary, orb.x));
        orb.angle = Math.PI - orb.angle;
        orb.targetAngle = orb.angle + randomBetween(-0.5, 0.5);
      }
      if (orb.y < -boundary || orb.y > height + boundary) {
        orb.y = Math.min(height + boundary, Math.max(-boundary, orb.y));
        orb.angle = -orb.angle;
        orb.targetAngle = orb.angle + randomBetween(-0.5, 0.5);
      }
    }
  };

  const animate = (frameTime: number): void => {
    const elapsedSeconds = Math.min(0.1, Math.max(0, (frameTime - lastFrameTime) / 1000));
    lastFrameTime = frameTime;
    update(elapsedSeconds);
    render();
    animationFrame = window.requestAnimationFrame(animate);
  };

  const syncMotionPreference = (): void => {
    window.cancelAnimationFrame(animationFrame);
    if (motionPreference.matches) {
      render();
      return;
    }
    lastFrameTime = performance.now();
    animationFrame = window.requestAnimationFrame(animate);
  };

  resize();
  syncMotionPreference();
  window.addEventListener("resize", resize, { passive: true });
  motionPreference.addEventListener("change", syncMotionPreference);

  window.addEventListener("beforeunload", () => {
    window.cancelAnimationFrame(animationFrame);
    window.removeEventListener("resize", resize);
    motionPreference.removeEventListener("change", syncMotionPreference);
  }, { once: true });
}
