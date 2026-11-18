interface RememberMeProps {
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}

export function RememberMe({ checked, disabled, onChange }: RememberMeProps) {
  return <label className="auth-remember">
    <input
      name="remember_me"
      type="checkbox"
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
      disabled={disabled}
    />
    <span>Remember me</span>
  </label>;
}
