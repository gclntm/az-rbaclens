import { XClose } from "@untitledui/icons";

/** The × that closes a panel or a dialog, in Untitled UI's look (44px target). */
export default function CloseButton({ onClick, label = "Close" }: { onClick: () => void; label?: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="uui flex size-11 flex-none cursor-pointer items-center justify-center rounded-lg text-fg-quaternary outline-focus-ring transition duration-100 ease-linear hover:bg-primary_hover hover:text-fg-quaternary_hover focus-visible:outline-2 focus-visible:outline-offset-2"
    >
      <XClose aria-hidden="true" className="size-5" />
    </button>
  );
}
