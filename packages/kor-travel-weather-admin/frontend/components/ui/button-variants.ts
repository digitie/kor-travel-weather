import { buttonVariants as commonButtonVariants } from "@kor-travel/ui/button-variants";

import { cn } from "@/lib/utils";

/**
 * disabled 설명(title) 도달성만 덧붙인다. 색상·크기·포커스 링은 공통 정본(@kor-travel/ui 0.2.3:
 * outline 2px·offset 2px·`--kt-focus`)이다 — 여기서 outline/ring을 다시 선언하면 링이 겹친다.
 * root는 흐리지 않고(링까지 흐려진다) 라벨 래퍼만 흐린다(`button.tsx`).
 */
const buttonInteractionClasses =
  "disabled:opacity-100 disabled:pointer-events-auto";

/** Link/a 소비자도 같은 공통 recipe와 Map 상호작용 정책을 쓴다. */
function buttonVariants(options: Parameters<typeof commonButtonVariants>[0] = {}) {
  const { class: classValue, className, ...variants } = options ?? {};
  return cn(
    commonButtonVariants(variants),
    buttonInteractionClasses,
    classValue,
    className,
  );
}

export { buttonVariants, buttonInteractionClasses };
