class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = 16000 / sampleRate;
    this.phase = 0;
    this.samples = new Int16Array(640);
    this.offset = 0;
  }

  process(inputs, outputs) {
    // Keep the node connected without sending microphone audio to speakers.
    for (const channel of outputs[0] || []) channel.fill(0);
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.phase += this.ratio;
      if (this.phase < 1) continue;
      this.phase -= 1;
      const value = Math.max(-1, Math.min(1, input[i]));
      this.samples[this.offset++] = value < 0 ? value * 32768 : value * 32767;
      if (this.offset === this.samples.length) {
        this.port.postMessage(this.samples.buffer, [this.samples.buffer]);
        this.samples = new Int16Array(640);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-worklet', PcmWorklet);
