/**
 * BrandMark - the MauntingStudios mountain mark used in the header.
 *
 * Follows the Design-DNA `Brand.tsx` pattern: a rounded container with the
 * MSM logo image and an optional online status dot. Rendered with Design-DNA
 * tokens only (no raw hex colors).
 */

export interface BrandMarkProps {
  /** Override the logo source (tests / custom assets). */
  logoSrc?: string;
  /** Show the small mint "online" indicator dot. */
  status?: boolean;
  /** Size class override. */
  className?: string;
}

export function BrandMark({
  logoSrc = '/msm-logo.png',
  status = false,
  className = '',
}: BrandMarkProps) {
  return (
    <span
      className={
        'relative grid size-9 place-items-center overflow-hidden rounded-full border border-ring/20 bg-ring/10 shadow-glow shrink-0 ' +
        className
      }
      data-testid="brand-mark"
    >
      <img
        src={logoSrc}
        className="size-full rounded-full object-cover"
        alt="MSM Logo"
        loading="eager"
        decoding="async"
      />
      {status ? (
        <span
          className="absolute -right-0.5 -top-0.5 z-10 size-2.5 rounded-full border-2 border-background bg-success"
          aria-hidden="true"
        />
      ) : null}
    </span>
  );
}
