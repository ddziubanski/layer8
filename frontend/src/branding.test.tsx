import { render, screen } from "@testing-library/react";
import { Brand, DEFAULT_LOGO } from "./branding";
import type { Branding } from "./api/client";

const branding = { product_name: "Layer 8", logo_url: null } as Branding;

test("default branding shows the human-and-switch mark on a contrast tile", () => {
  const { container } = render(<Brand b={branding} />);
  expect(screen.getByText("Layer 8")).toBeInTheDocument();
  const logo = container.querySelector("img")!;
  expect(logo).toHaveAttribute("src", DEFAULT_LOGO);
  expect(logo).toHaveClass("brand-logo-default");
  expect(logo).toHaveAttribute("alt", "");
});

test("uploaded customer logos keep their original presentation", () => {
  const { container } = render(<Brand b={{ ...branding, logo_url: "/api/branding/logo" }} />);
  const logo = container.querySelector("img")!;
  expect(logo).toHaveAttribute("src", "/api/branding/logo");
  expect(logo).not.toHaveClass("brand-logo-default");
});
