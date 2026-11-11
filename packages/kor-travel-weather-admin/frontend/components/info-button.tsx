"use client";

import {
  Popover,
  PopoverTrigger,
  PopoverContent,
  Button,
} from "@kor-travel/ui/primitives";
import { useId, useState } from "react";
import { Info, X } from "lucide-react";
import type { ReactNode } from "react";

/** Transport와 같은 제목 옆 보조 설명. 오류·특보·갱신 지연은 접지 않는다. */
export function InfoButton({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const descriptionId = useId();
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className="info-button" aria-label={`${label} 안내`}>
        <Info size={15} aria-hidden="true" />
      </PopoverTrigger>
      <PopoverContent
        sideOffset={6}
        collisionPadding={12}
        className="info-popup"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <div className="info-popup-heading">
          <h2 id={titleId}>{label}</h2>
          <Button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="안내 닫기"
            className="info-button"
          >
            <X size={15} aria-hidden="true" />
          </Button>
        </div>
        <div id={descriptionId}>{children}</div>
      </PopoverContent>
    </Popover>
  );
}
