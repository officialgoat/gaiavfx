/** A form field's text, or null when it is empty or absent: an unset field never becomes the string "null". */
export function formValue(form: FormData, name: string): string | null {
  const value = form.get(name);
  return typeof value === "string" && value !== "" ? value : null;
}

/** A form field's text, empty when unset. */
export const formText = (form: FormData, name: string) =>
  formValue(form, name) ?? "";

/** A numeric field, or null when it is empty. */
export function formNumber(form: FormData, name: string): number | null {
  const value = formValue(form, name);
  return value === null ? null : Number(value);
}

/** A checkbox: present means checked. */
export const formChecked = (form: FormData, name: string) =>
  form.get(name) === "on";
