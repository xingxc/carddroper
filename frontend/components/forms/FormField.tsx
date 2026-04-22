import type { InputHTMLAttributes } from "react";

interface FormFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
  id: string;
}

/**
 * Label + input + field-level error message.
 * Used in ≥ 2 form pages, so extracted here.
 */
export function FormField({ label, error, id, className, ...rest }: FormFieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium text-gray-700">
        {label}
      </label>
      <input
        id={id}
        className={[
          "w-full rounded-md border px-3 py-2 text-sm outline-none",
          "focus:ring-2 focus:ring-blue-500 focus:border-blue-500",
          error
            ? "border-red-400 bg-red-50"
            : "border-gray-300 bg-white",
          className ?? "",
        ]
          .join(" ")
          .trim()}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        {...rest}
      />
      {error && (
        <p id={`${id}-error`} role="alert" className="text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
