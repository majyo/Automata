import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownContent } from "./MarkdownContent";

describe("MarkdownContent", () => {
  it("renders GFM markdown with highlighted code and safe external links", () => {
    const { container } = render(
      <MarkdownContent
        text={"## Title\n\n- one\n- two\n\n```ts\nconst answer: number = 42;\n```\n\n[docs](https://example.com)"}
      />,
    );

    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);

    const code = container.querySelector("pre code");
    expect(code).toHaveClass("hljs", "language-ts");
    expect(code?.querySelector(".hljs-keyword")).toHaveTextContent("const");

    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
  });

  it("does not render raw HTML from the source text", () => {
    render(<MarkdownContent text={'before <script>alert("x")</script> after'} />);
    expect(document.querySelector("script")).not.toBeInTheDocument();
    expect(screen.getByText(/before/)).toBeInTheDocument();
  });
});
