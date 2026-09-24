/**
 * The handful of glyphs the shell needs, drawn here rather than imported: a
 * device's controls want a mic, a speaker and a caption mark, and none of
 * those are in the Ionicons subset `fanmoot` vendors. Stroke icons on a
 * 24-unit grid, `currentColor` throughout, so they take the colour of
 * whatever they sit in.
 */
import type { SVGProps } from 'react'

type Props = SVGProps<SVGSVGElement> & { size?: number }

function Svg({ size = 20, children, ...rest }: Props) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

export const MicIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" />
  </Svg>
)

export const MicOffIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M15 10V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 4.6 2.5M5.5 11a6.5 6.5 0 0 0 10.4 5.2M18.5 11c0 .8-.1 1.5-.4 2.2M12 17.5V21M8.5 21h7M4 4l16 16" />
  </Svg>
)

export const SendIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M5 12h13M13 6l6 6-6 6" />
  </Svg>
)

export const StopIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none" />
  </Svg>
)

export const SpeakerIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
    <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" />
  </Svg>
)

export const SpeakerOffIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
    <path d="M16 9.5l5 5M21 9.5l-5 5" />
  </Svg>
)

export const CaptionsIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="3" y="5.5" width="18" height="13" rx="2.5" />
    <path d="M10.5 10.2a2.2 2.2 0 1 0 0 3.6M17 10.2a2.2 2.2 0 1 0 0 3.6" />
  </Svg>
)

export const CloseIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Svg>
)

export const ExpandIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
  </Svg>
)

export const ExternalIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </Svg>
)

export const PlusIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
)

export const MinusIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M5 12h14" />
  </Svg>
)

export const CheckIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Svg>
)

export const AlertIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M12 8v5M12 16.5v.01M10.3 3.9L2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
  </Svg>
)
