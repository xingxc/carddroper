interface FormErrorProps {
  message: string | null | undefined;
}

/**
 * Form-level error banner. Renders nothing when `message` is falsy.
 * Used in ≥ 2 form pages, so extracted here.
 */
export function FormError({ message }: FormErrorProps) {
  if (!message) return null;

  return (
    <div
      role="alert"
      className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700"
    >
      {message}
    </div>
  );
}
