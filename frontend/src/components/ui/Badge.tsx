import React from 'react'

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'success' | 'warning' | 'destructive' | 'info'
}

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className = '', variant = 'default', children, ...props }, ref) => {
    const variants = {
      default: 'bg-muted text-muted-foreground border-border',
      success: 'bg-success/10 text-success border-success/30',
      warning: 'bg-warning/10 text-warning border-warning/30',
      destructive: 'bg-destructive/10 text-destructive border-destructive/30',
      info: 'bg-primary/10 text-primary border-primary/30',
    }

    return (
      <span
        ref={ref}
        className={`
          inline-flex items-center rounded-full border px-2.5 py-0.5
          text-xs font-medium transition-colors
          ${variants[variant]}
          ${className}
        `}
        {...props}
      >
        {children}
      </span>
    )
  }
)
Badge.displayName = 'Badge'
