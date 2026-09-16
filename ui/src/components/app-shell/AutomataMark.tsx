export function AutomataMark({ className = "" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
    >
      <path d="M5 25 16 5l11 20H5Z" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="m11 25 5-9 5 9M9 18h14"
        stroke="currentColor"
        strokeWidth="1.6"
      />
    </svg>
  );
}
