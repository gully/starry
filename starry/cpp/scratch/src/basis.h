/**
Elliptic integrals computed following:

      Bulirsch 1965, Numerische Mathematik, 7, 78
      Bulirsch 1965, Numerische Mathematik, 7, 353

and the implementation by E. Agol (private communication).
Adapted from DFM's AstroFlow: https://github.com/dfm/AstroFlow/
*/

#ifndef _STARRY_BASIS_H_
#define _STARRY_BASIS_H_

#include <cmath>

namespace basis {

using std::abs;

    // Evaluate a polynomial vector at a given (x, y) coordinate
    template <typename T>
    T poly (const int lmax, const Eigen::Matrix<T, Eigen::Dynamic, 1>& p, const T& x, const T&y) {
        // Check if outside the sphere
        if (x * x + y * y > 1) return NAN;
        int N = (lmax + 1) * (lmax + 1);
        int l, m, mu, nu, n = 0;
        T z = sqrt(1.0 - x * x - y * y);
        Eigen::Matrix<T, Eigen::Dynamic, 1> basis(N);

        // Compute the basis
        for (l=0; l<lmax+1; l++) {
            for (m=-l; m<l+1; m++) {
                mu = l - m;
                nu = l + m;
                if ((nu % 2) == 0)
                    basis(n) = pow(x, mu / 2) * pow(y, nu / 2);
                else
                    basis(n) = pow(x, (mu - 1) / 2) * pow(y, (nu - 1) / 2) * z;
                n++;
            }
        }

        // Dot the coefficients in
        return p.dot(basis);

    }


}; // namespace basis

#endif