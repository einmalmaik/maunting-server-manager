export interface DisBadgeProps {
  size?: number;
  showText?: boolean;
  className?: string;
}

const cn = (...classes: any[]) => classes.filter(Boolean).join(' ');

export const DisBadge = ({ size = 24, showText = true, className }: DisBadgeProps) => {
  return (
    <a
      href="https://dis.mauntingstudios.de"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Powered by DIS - Defensive Integration Shield"
      title="Defensive Integration Shield (Öffnet in neuem Tab)"
      className={cn(
        'inline-flex items-center gap-2 rounded-full border border-ice-300/10 bg-ice-500/10 px-2 py-0.5 backdrop-blur-sm transition-all hover:bg-ice-500/25 hover:border-ice-300/25 cursor-pointer',
        className,
      )}
    >
      <span
        className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-ice-300/20 bg-black/40 shadow-sm"
        style={{ width: size, height: size }}
      >
        <img
          src="/dis-logo.png"
          alt=""
          aria-hidden="true"
          width={size}
          height={size}
          className="h-full w-full object-cover rounded-full"
          loading="eager"
          decoding="async"
        />
      </span>
      {showText && (
        <span className="text-[9px] font-semibold uppercase tracking-[0.18em] text-ice-200/80">
          Powered by DIS
        </span>
      )}
    </a>
  );
};
