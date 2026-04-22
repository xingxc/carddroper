import type { ButtonHTMLAttributes } from "react";

interface SubmitButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  isPending: boolean;
  label: string;
  pendingLabel?: string;
}

/**
 * Submit button with an inline pending state.
 * Used in ≥ 2 form pages, so extracted here.
 */
export function SubmitButton({
  isPending,
  label,
  pendingLabel = "Please wait…",
  disabled,
  className,
  ...rest
}: SubmitButtonProps) {
  return (
    <button
      type="submit"
      disabled={isPending || disabled}
      className={[
        "flex w-full items-center justify-center rounded-md px-4 py-2 text-sm font-semibold",
        "bg-blue-600 text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2",
        "disabled:opacity-60 disabled:cursor-not-allowed",
        "transition-colors",
        className ?? "",
      ]
        .join(" ")
        .trim()}
      {...rest}
    >
      {isPending ? (
        <span className="flex items-center gap-2">
          <svg
            className="h-4 w-4 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
            />
          </svg>
          {pendingLabel}
        </span>
      ) : (
        label
      )}
    </button>
  );
}
