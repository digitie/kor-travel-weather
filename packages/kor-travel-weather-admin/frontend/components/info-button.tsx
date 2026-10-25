"use client";

import { Popover } from "@base-ui/react/popover";
import { Info, X } from "lucide-react";
import type { ReactNode } from "react";

/** Transport와 같은 제목 옆 보조 설명. 오류·특보·갱신 지연은 접지 않는다. */
export function InfoButton({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Popover.Root>
      <Popover.Trigger className="info-button" aria-label={`${label} 안내`}>
        <Info size={15} aria-hidden="true" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={6} collisionPadding={12} className="info-positioner">
          <Popover.Popup className="info-popup">
            <div className="info-popup-heading">
              <Popover.Title>{label}</Popover.Title>
              <Popover.Close aria-label="안내 닫기" className="info-button"><X size={15} aria-hidden="true" /></Popover.Close>
            </div>
            <Popover.Description render={<div />}>{children}</Popover.Description>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
