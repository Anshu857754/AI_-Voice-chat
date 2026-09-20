// Waveform - a tiny bar visualiser. Static-low when idle, animated when `active`.

const DELAYS = [0, 0.15, 0.3, 0.1, 0.25, 0.05, 0.2, 0.35]

export default function Waveform({ active = false, color = 'currentColor', bars = 4, height = 14, className = '' }) {
  return (
    <span
      className={`inline-flex items-center gap-[2px] ${active ? 'wave-on' : ''} ${className}`}
      style={{ height }}
      aria-hidden="true"
    >
      {Array.from({ length: bars }, (_, i) => (
        <span
          key={i}
          className="wave-bar block w-[2px] rounded-full"
          style={{ height: '100%', background: color, animationDelay: `${DELAYS[i % DELAYS.length]}s` }}
        />
      ))}
    </span>
  )
}
