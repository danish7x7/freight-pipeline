import type { ButtonHTMLAttributes, ReactNode } from "react";

const VARIANTS = {
  primary: "bg-gray-900 text-white hover:bg-gray-700",
  danger: "bg-red-600 text-white hover:bg-red-500",
  ghost: "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50",
};

export function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof VARIANTS;
  children: ReactNode;
}) {
  return (
    <button
      className={`rounded-md px-4 py-2 text-sm font-medium disabled:opacity-50 ${VARIANTS[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-gray-200 bg-white p-4 shadow-sm ${className}`}
    >
      {children}
    </div>
  );
}
