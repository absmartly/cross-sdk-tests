import '@angular/compiler';
import { enableProdMode, Component, ErrorHandler, provideExperimentalZonelessChangeDetection } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { renderApplication, provideServerRendering } from '@angular/platform-server';
import { ABSmartlyService, TreatmentComponent, TreatmentVariantDirective } from '@absmartly/angular-sdk';

enableProdMode();

// One template per variant in the experiment. An assignment outside them hits
// the `default` template, which the caller reports instead of reading it as 0.
export async function renderTreatment(service, name, peek = false, variantCount = 1) {
  const errors = [];
  const variantTemplates = Array.from({ length: Math.max(1, variantCount) }, (_, i) =>
    `<ng-template [absTreatmentVariant]="${i}"><span data-variant="${i}"></span></ng-template>`).join('');
  const Host = Component({
    selector: 'app-root',
    standalone: true,
    imports: [TreatmentComponent, TreatmentVariantDirective],
    template: `<abs-treatment [name]="name" [peek]="peek">${variantTemplates}` +
      `<ng-template absTreatmentVariant="loading"><span data-loading></span></ng-template>` +
      `<ng-template absTreatmentVariant="default"><span data-unmatched></span></ng-template>` +
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
