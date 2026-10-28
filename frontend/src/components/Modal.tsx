import { useEffect, useRef, type HTMLAttributes } from 'react';
import { createPortal } from 'react-dom';
import './modal.css';

let openModals = 0;
let originalOverflow = '';

/** All blocking dialogs live outside page stacking contexts; inline task steps stay inline. */
export default function Modal({ children, className = '', scope = '', inline = false, ...props }:
  HTMLAttributes<HTMLDivElement> & { scope?: string; inline?: boolean }) {
  const overlay = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (inline) return;
    const previous = document.activeElement as HTMLElement | null;
    if (openModals++ === 0) {
      originalOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    }
    const controls = () => Array.from(overlay.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]') ?? [])
      .filter(element => element.getClientRects().length > 0);
    if (!overlay.current?.contains(document.activeElement)) (controls()[0] ?? overlay.current)?.focus();
    const trap = (event: KeyboardEvent) => {
      const layers = document.querySelectorAll('.app-modal-backdrop');
      if (event.defaultPrevented || layers[layers.length - 1] !== overlay.current || event.key !== 'Tab') return;
      const items = controls();
      const index = items.indexOf(document.activeElement as HTMLElement);
      if (!items.length) { event.preventDefault(); overlay.current?.focus(); }
      else if (event.shiftKey && index <= 0) { event.preventDefault(); items[items.length - 1].focus(); }
      else if (!event.shiftKey && (index < 0 || index === items.length - 1)) { event.preventDefault(); items[0].focus(); }
    };
    document.addEventListener('keydown', trap);
    return () => {
      document.removeEventListener('keydown', trap);
      if (--openModals === 0) document.body.style.overflow = originalOverflow;
      if (previous?.isConnected) previous.focus();
    };
  }, [inline]);
  if (inline) return <div {...props} className={className}>{children}</div>;
  return createPortal(<div className="modal-root home-workspace"><div className={`modal-context ${scope}`}>
    <div {...props} ref={overlay} tabIndex={-1} className={`app-modal-backdrop ${className}`}>{children}</div>
  </div></div>, document.body);
}
