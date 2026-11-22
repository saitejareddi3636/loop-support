import { useEffect, useRef } from 'react';

export type OrbState = 'disconnected' | 'connecting' | 'listening' | 'processing' | 'speaking' | 'interrupted' | 'muted';

export default function SignalOrb({ state, input, output }: { state: OrbState; input: AnalyserNode | null; output: AnalyserNode | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d', { alpha: true });
    if (!canvas || !context) return;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let last = 0;
    let level = 0;
    const inputData = input ? new Uint8Array(input.fftSize) : null;
    const outputData = output ? new Uint8Array(output.fftSize) : null;
    const resize = () => {
      const size = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(size.width * dpr);
      canvas.height = Math.round(size.height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    function rms(analyser: AnalyserNode | null, data: Uint8Array<ArrayBuffer> | null) {
      if (!analyser || !data) return 0;
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i += 2) {
        const sample = (data[i] - 128) / 128;
        sum += sample * sample;
      }
      return Math.min(1, Math.sqrt(sum / (data.length / 2)) * 5);
    }

    function draw(time: number) {
      if (!canvas || !context) return;
      if (media.matches && last) return;
      if (time - last < (media.matches ? 0 : 25)) { frame = requestAnimationFrame(draw); return; }
      last = time;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      const size = Math.min(width, height);
      const cx = width / 2;
      const cy = height / 2;
      const active = state === 'listening' || state === 'speaking';
      const measured = state === 'speaking' ? rms(output, outputData) : state === 'listening' ? rms(input, inputData) : 0;
      level += (measured - level) * (measured > level ? .32 : .12);
      const subdued = state === 'disconnected' || state === 'muted';
      const radius = size * .295;
      const phase = media.matches ? 0 : time * .00025;
      context.clearRect(0, 0, width, height);

      // A sparse instrument dial gives the field a sense of scale without a dashboard.
      context.beginPath();
      context.arc(cx, cy, radius * 1.49, 0, Math.PI * 2);
      context.strokeStyle = subdued ? 'rgba(117,153,138,.17)' : 'rgba(145,243,200,.14)';
      context.lineWidth = 1;
      context.stroke();
      for (let tick = 0; tick < 36; tick++) {
        const a = (tick / 36) * Math.PI * 2 - Math.PI / 2;
        const inner = radius * (tick % 3 === 0 ? 1.42 : 1.45);
        const outer = radius * 1.49;
        context.beginPath();
        context.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner);
        context.lineTo(cx + Math.cos(a) * outer, cy + Math.sin(a) * outer);
        context.strokeStyle = 'rgba(145,243,200,.22)';
        context.stroke();
      }

      const halo = context.createRadialGradient(cx, cy, radius * .2, cx, cy, radius * 1.38);
      halo.addColorStop(0, subdued ? 'rgba(72,111,98,.08)' : 'rgba(145,243,200,.16)');
      halo.addColorStop(.65, subdued ? 'rgba(52,86,73,.09)' : 'rgba(48,156,116,.11)');
      halo.addColorStop(1, 'rgba(0,0,0,0)');
      context.fillStyle = halo;
      context.beginPath();
      context.arc(cx, cy, radius * 1.38, 0, Math.PI * 2);
      context.fill();

      const fill = context.createRadialGradient(cx - radius * .26, cy - radius * .3, radius * .02, cx, cy, radius * 1.2);
      fill.addColorStop(0, subdued ? '#749888' : '#d5ffe8');
      fill.addColorStop(.28, subdued ? '#426e5c' : '#95e8c7');
      fill.addColorStop(.7, subdued ? '#244b3e' : '#318a6d');
      fill.addColorStop(1, '#11342e');
      context.beginPath();
      for (let point = 0; point <= 144; point++) {
        const a = point / 144 * Math.PI * 2;
        const audioShape = active ? level * radius * .13 * (Math.sin(5 * a + phase * 5) * .55 + Math.sin(8 * a - phase * 3) * .45) : 0;
        const shape = radius + audioShape + (state === 'processing' || state === 'connecting' ? Math.sin(3 * a + phase * 8) * radius * .012 : 0);
        const x = cx + Math.cos(a) * shape;
        const y = cy + Math.sin(a) * shape;
        if (point === 0) context.moveTo(x, y); else context.lineTo(x, y);
      }
      context.closePath();
      context.fillStyle = fill;
      context.fill();
      context.strokeStyle = subdued ? 'rgba(142,181,161,.36)' : 'rgba(192,255,219,.65)';
      context.lineWidth = 1.25;
      context.stroke();

      context.save();
      context.beginPath(); context.arc(cx, cy, radius * .97, 0, Math.PI * 2); context.clip();
      for (let band = 0; band < 6; band++) {
        context.beginPath();
        const y = cy - radius * .38 + band * radius * .17;
        context.moveTo(cx - radius * 1.1, y);
        context.bezierCurveTo(cx - radius * .35, y - radius * (.18 + level * .12), cx + radius * .22, y + radius * (.12 + level * .1), cx + radius * 1.1, y - radius * .1);
        context.strokeStyle = `rgba(224,255,236,${subdued ? .045 : .08 + band * .012})`;
        context.lineWidth = 1;
        context.stroke();
      }
      context.restore();
      if (!media.matches) frame = requestAnimationFrame(draw);
    }
    frame = requestAnimationFrame(draw);
    const refresh = () => { last = 0; cancelAnimationFrame(frame); frame = requestAnimationFrame(draw); };
    media.addEventListener('change', refresh);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); media.removeEventListener('change', refresh); };
  }, [state, input, output]);

  return <canvas className="signal-orb" ref={canvasRef} role="img" aria-label={`Voice visualization: ${state}`} />;
}
