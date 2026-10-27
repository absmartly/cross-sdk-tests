// Renders the Angular SDK's real <abs-treatment> component (with one
// absTreatmentVariant template per variant, plus its loading and error
// templates) through Angular's server renderer, and returns the HTML it
// produced plus any error Angular reported while rendering it.
import '@angular/compiler';
import { enableProdMode, Component, ErrorHandler, provideExperimentalZonelessChangeDetection } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { renderApplication, provideServerRendering } from '@angular/platform-server';
import { ABSmartlyService, TreatmentComponent, TreatmentVariantDirective } from '@absmartly/angular-sdk';

enableProdMode();
const variantTemplates = Array.from({ length: 11 }, (_, i) =>
  `<ng-template [absTreatmentVariant]="${i}"><span data-variant="${i}"></span></ng-template>`).join('');

export async function renderTreatment(service, name, peek = false) {
  const errors = [];
  const Host = Component({
    selector: 'app-root',
    standalone: true,
    imports: [TreatmentComponent, TreatmentVariantDirective],
    template: `<abs-treatment [name]="name" [peek]="peek">${variantTemplates}` +
      `<ng-template absTreatmentVariant="loading"><span data-loading></span></ng-template>` +
      `<ng-template absTreatmentVariant="error"><span data-error></span></ng-template></abs-treatment>`,
  })(class Host { name = name; peek = peek; });

  const html = await renderApplication(
    (bootstrapContext) => bootstrapApplication(Host, {
      providers: [
        provideServerRendering(),
        provideExperimentalZonelessChangeDetection(),
        { provide: ABSmartlyService, useValue: service },
        { provide: ErrorHandler, useValue: { handleError: (e) => errors.push(e) } },
      ],
    }, bootstrapContext),
    { document: '<app-root></app-root>' },
  );
  return { html, errors };
}

