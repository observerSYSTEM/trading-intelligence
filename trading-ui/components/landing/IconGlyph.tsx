type IconName =
  | "bias"
  | "target"
  | "pro"
  | "elite"
  | "telegram"
  | "workflow"
  | "gold"
  | "intraday"
  | "structure"
  | "notifications"
  | "account"
  | "plan";

type IconGlyphProps = {
  name: IconName;
  className?: string;
};

function pathForIcon(name: IconName): string {
  switch (name) {
    case "bias":
      return "M4 16l4-4 4 2 6-8";
    case "target":
      return "M12 5v14M5 12h14M8 8l8 8M16 8l-8 8";
    case "pro":
      return "M4 18l4-9 4 5 4-8 4 12";
    case "elite":
      return "M12 4l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 18l-5.6 3 1.1-6.2L3 10.6l6.2-.9L12 4z";
    case "telegram":
      return "M4 11l16-7-4 16-5-4-3 3 .6-4.6L4 11z";
    case "workflow":
      return "M6 7h12M6 12h8M6 17h12";
    case "gold":
      return "M6 8h12l-2 8H8L6 8zM9 8l1-3h4l1 3";
    case "intraday":
      return "M5 16V8m4 8V5m5 11v-6m5 6V7";
    case "structure":
      return "M4 18h16M6 18V9h3v9m4 0V6h3v12m4 0v-7h2v7";
    case "notifications":
      return "M12 4a4 4 0 0 1 4 4v3l1.5 2.5V15h-11v-1.5L8 11V8a4 4 0 0 1 4-4zm-2 13h4";
    case "account":
      return "M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm-7 8a7 7 0 0 1 14 0";
    case "plan":
      return "M5 5h14v14H5zM5 10h14M10 5v14";
    default:
      return "M4 12h16";
  }
}

export default function IconGlyph({ name, className }: IconGlyphProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ?? "h-5 w-5"}
      aria-hidden="true"
    >
      <path d={pathForIcon(name)} />
    </svg>
  );
}

