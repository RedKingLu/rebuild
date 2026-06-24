import type { ReactNode, ButtonHTMLAttributes } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'warning' | 'mock';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children?: ReactNode;
}

const SIZE_STYLES: Record<ButtonSize, React.CSSProperties> = {
  sm:   { height: 28, padding: '0 10px', fontSize: 12, gap: 4 },
  md:   { height: 36, padding: '0 14px', fontSize: 14, gap: 6 },
  lg:   { height: 44, padding: '0 18px', fontSize: 15, gap: 8 },
  icon: { width: 32, height: 32, padding: 0, minWidth: 32 },
};

const VARIANT_STYLES: Record<ButtonVariant, React.CSSProperties> = {
  primary:   { background: 'var(--color-primary)', color: '#fff', borderColor: 'var(--color-primary)' },
  secondary: { background: 'var(--color-surface)', color: 'var(--color-primary)', borderColor: 'var(--color-primary-border)' },
  ghost:     { background: 'transparent', color: 'var(--color-text-muted)', borderColor: 'transparent' },
  danger:    { background: 'var(--color-danger)', color: '#fff', borderColor: 'var(--color-danger)' },
  warning:   { background: 'var(--color-warning-soft)', color: '#92400e', borderColor: '#fcd34d' },
  mock:      { background: 'var(--color-mock-soft)', color: 'var(--color-mock)', borderColor: 'var(--color-mock-border)' },
};

export function Button({ variant = 'primary', size = 'md', children, style, disabled, ...props }: Props) {
  return (
    <button
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
        borderRadius: 'var(--radius-md)', border: '1px solid transparent',
        fontWeight: 600, cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        ...SIZE_STYLES[size],
        ...VARIANT_STYLES[variant],
        ...style,
      }}
      disabled={disabled}
      {...props}
    >
      {children}
    </button>
  );
}
