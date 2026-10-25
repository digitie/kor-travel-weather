"use client";

import { ChevronDown, SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export type SourceOption = { value: string; label: string; count?: number };

type Props = {
  id?: string;
  options: SourceOption[];
  // null은 전체, 빈 배열은 선택 없음이다. 둘을 구별해 전체 해제도 지원한다.
  value: string[] | null;
  onChange: (value: string[] | null) => void;
  fallbackFocusRef?: RefObject<HTMLButtonElement | null>;
};

export function SourceMultiSelect({ id, options, value, onChange, fallbackFocusRef }: Props) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const trigger = triggerRef.current;
    if (!open || !trigger || typeof ResizeObserver === "undefined") return;
    // 필터가 접혀도 portal 메뉴는 남으므로 자신의 기준점이 숨겨지면 닫는다.
    const observer = new ResizeObserver(() => {
      if (!trigger.getClientRects().length) setOpen(false);
    });
    observer.observe(trigger);
    return () => observer.disconnect();
  }, [open]);
  const selected = options.filter((option) => value === null || value.includes(option.value));
  const label = value === null ? "모든 소스" : selected.length === 0 ? "소스 선택"
    : selected.length === 1 ? selected[0].label : `${selected.length}개 소스`;
  return (
    <DropdownMenu modal={false} open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button ref={triggerRef} id={id} type="button" className="kt-map-select-trigger" aria-label="날씨 소스 필터" disabled={!options.length}>
          <SlidersHorizontal size={15} aria-hidden="true" />
          <span>{label}</span><ChevronDown size={14} aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent ref={contentRef} align="start" aria-label="날씨 소스 선택" onCloseAutoFocus={(event) => {
        if (!triggerRef.current?.getClientRects().length) {
          event.preventDefault();
          const active = document.activeElement;
          if (active instanceof HTMLElement && active !== document.body && active !== document.documentElement
            && !contentRef.current?.contains(active) && active.getClientRects().length) return;
          fallbackFocusRef?.current?.focus({ preventScroll: true });
        }
      }}>
        <DropdownMenuLabel className="multi-select-label">날씨 데이터 제공처</DropdownMenuLabel>
        <DropdownMenuItem className="multi-select-action" onSelect={(event) => { event.preventDefault(); onChange(null); }}>전체 선택</DropdownMenuItem>
        <DropdownMenuItem className="multi-select-action" onSelect={(event) => { event.preventDefault(); onChange([]); }}>전체 해제</DropdownMenuItem>
        <DropdownMenuSeparator className="multi-select-separator" />
        {options.map((option) => (
          <DropdownMenuCheckboxItem key={option.value} checked={value === null || value.includes(option.value)}
            onSelect={(event) => event.preventDefault()}
            onCheckedChange={(checked) => {
              const next = new Set(selected.map((item) => item.value));
              if (checked) next.add(option.value); else next.delete(option.value);
              onChange(next.size === options.length ? null
                : options.filter((item) => next.has(item.value)).map((item) => item.value));
            }}>
            <span className="multi-select-option-label">{option.label}</span>{option.count !== undefined ? <span className="multi-select-count">{option.count}</span> : null}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
