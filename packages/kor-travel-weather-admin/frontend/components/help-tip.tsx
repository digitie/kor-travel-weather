"use client";
// Hallmark · genre: editorial-utilitarian · macrostructure: Rail-Workbench · design-system: design.md · designed-as-app

import * as React from "react";
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import { CircleHelpIcon, InfoIcon } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

type HelpTipProps = {
  /** 도움말 대상 필드/항목 이름 — 접근성 이름 `도움말: {label}`을 만든다. */
  label: string;
  children: React.ReactNode;
  /** 트리거 버튼에 붙는 클래스. */
  className?: string;
  /** 팝오버 본문에 붙는 클래스(길이가 긴 본문의 높이 제한·스크롤 등). */
  contentClassName?: string;
  variant?: "help" | "info";
};

/**
 * 필드 옆 도움말 아이콘 버튼 (§3/§5-6).
 * hover(800ms) / focus(0ms) = Tooltip(빠른 훑기), click = Popover(터치 기기 안전 + 긴 내용).
 * 본문 상세 설명을 인라인 hint 대신 여기로 옮긴다.
 *
 * Hit target: 24px visible box + `before:` pseudo-element extending the pointer target to 40px
 * (design.md §Microinteractions ≥ 24px) without changing the 14px glyph or inline layout.
 * States: rest ink-2 · hover/open ink on paper-2 · active paper-3 · focus = one outline recipe.
 */
function HelpTip({
  label,
  children,
  className,
  contentClassName,
  variant = "help",
}: HelpTipProps) {
  const Icon = variant === "info" ? InfoIcon : CircleHelpIcon;
  const [store] = React.useState(() =>
    createStore<{ open: boolean }>(() => ({ open: false })),
  );
  const popoverOpen = useStore(store, (state) => state.open);
  const setPopoverOpen = (open: boolean) => store.setState({ open });
  // 팝오버를 닫으면 포커스가 트리거로 돌아와 긴 본문 툴팁이 다시 떠서 아래 탭·컨트롤을 가린다.
  // 그 복귀 포커스로 여는 툴팁만 막고, 포인터가 다시 들어오거나 포커스가 빠지면 푼다.
  const [tipOpen, setTipOpen] = React.useState(false);
  const suppressTipRef = React.useRef(false);

  const trigger = (
    <button
      aria-label={`도움말: ${label}`}
      onBlur={() => {
        suppressTipRef.current = false;
      }}
      onPointerEnter={() => {
        suppressTipRef.current = false;
      }}
      className={cn(
        "relative inline-flex size-6 shrink-0 items-center justify-center rounded-control text-text-secondary transition-[color,background-color] duration-fast ease-out",
        "before:absolute before:-inset-2",
        "hover:bg-surface-subtle hover:text-text-primary active:bg-surface-muted aria-expanded:bg-surface-subtle aria-expanded:text-text-primary",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
        "disabled:pointer-events-none disabled:text-text-disabled",
        className,
      )}
      type="button"
    >
      <Icon aria-hidden className="size-3.5" />
    </button>
  );

  return (
    <Popover
      open={popoverOpen}
      onOpenChange={(open) => {
        if (!open) suppressTipRef.current = true;
        setPopoverOpen(open);
      }}
    >
      <TooltipProvider>
        <Tooltip
          open={tipOpen && !popoverOpen}
          onOpenChange={(open) => {
            if (open && suppressTipRef.current) return;
            setTipOpen(open);
          }}
        >
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>{trigger}</PopoverTrigger>
          </TooltipTrigger>
          {popoverOpen ? null : <TooltipContent>{children}</TooltipContent>}
        </Tooltip>
      </TooltipProvider>
      <PopoverContent
        align="start"
        className={cn("leading-relaxed", contentClassName)}
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}

export { HelpTip };
export type { HelpTipProps };
