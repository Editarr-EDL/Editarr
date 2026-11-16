import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { OperatorAccess } from "./operator-access";
import "../../styles/community-operator.css";

createRoot(document.getElementById("root")!).render(<StrictMode><OperatorAccess /></StrictMode>);